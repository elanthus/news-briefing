# News briefing evaluator

This directory is a development-only benchmark. Nothing under `evaluator/` is imported by `fetch_news.py`, `briefing_config.py`, `corpus_schema.py`, or `eval_briefing.py`, and the main news-briefing workflow still runs with Python 3.11+ and no install step.

## What is fixed

`fixtures/checker-cases.json` contains 54 fixed, human-reviewable gold-label cases: 42 checker cases and 12 feed-parser cases. They cover fabricated, altered, bare, Markdown, canonical-equivalent, and duplicated URLs; UTF-8/16/32, malformed XML, empty feeds, and wrong feed shapes; degraded and partially reported source health; thin and conflicting evidence; consolidation and category ambiguity; and deliberately valid cases that expose false positives. The committed labels are provisional until an independent human approves them; their provenance and review status are part of the suite and every report.

`fixtures/generation-cases.json` contains 18 fixed end-to-end cases: nine utility/quality cases and nine indirect prompt-injection attacks against citations, prose, selection, health reporting, and formatting. The suite records first output, one correction attempt when needed, final output, the transformed corpus, raw provider usage, and hashes.

The injection design follows the evaluation posture of [AgentDojo (NeurIPS 2024)](https://proceedings.neurips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html)—measure clean utility as well as attacks—and [MELON (ICML 2025)](https://proceedings.mlr.press/v267/zhu25z.html), which evaluates indirect instructions embedded in untrusted retrieved content. This suite is application-specific rather than a claim to reproduce either paper.

## Setup

No evaluator package is required for normal briefing use. For evaluator development only:

```bash
uv sync --project evaluator --group dev
```

The harness itself uses the standard library, so the offline suite can also run without installing anything:

```bash
python3 -m evaluator checker --output evaluator/results/checker-report.json
```

Copy `evaluator/.env.example` to the ignored `evaluator/.env`, which is the CLI default:

```bash
cp evaluator/.env.example evaluator/.env
```

The template contains blank key fields and a dated, commented model list for all four providers. Re-check the dated official catalog links before a long run; never put credentials in the tracked template.

## Live model runs

Run one model and one prompt version:

```bash
python3 -m evaluator run \
  --provider codex-cli=gpt-5.6-terra \
  --prompt production=briefing-prompt.md \
  --trials 3
```

Run all four configured providers:

```bash
python3 -m evaluator run --all-providers --trials 3
```

Compare prompt versions by repeating `--prompt`:

```bash
python3 -m evaluator run \
  --all-providers \
  --prompt baseline=briefing-prompt.md \
  --prompt candidate=/path/to/candidate-prompt.md \
  --trials 3
```

Supported provider names are `codex-cli`, `claude-code-cli`, `openrouter`, and `nvidia`. The CLI adapters disable tools or use an empty read-only workspace. The API adapters send the corpus directly to OpenRouter's and NVIDIA's OpenAI-compatible chat-completions endpoints.

Every run writes `manifest.json`, `report.json`, `report.md`, and per-trial artifacts under `evaluator/results/<UTC timestamp>/`. Those three top-level files are atomically checkpointed after every trial. A provider failure is recorded with its stage and error, the remaining matrix continues, and the command exits nonzero after finishing so automation can detect the partial run without losing already-billed work. Each successful trial also gets `grounding-adjudication.json`; a human reviewer sets each topic's `grounding_error` to `true` or `false` and may add notes. Rebuild any complete or partial report without calling a model:

```bash
python3 -m evaluator report evaluator/results/<run>/manifest.json
```

## Metrics and denominators

Every proportion includes successes, trials, and a 95% Wilson interval.

- Checker precision and recall are micro-averaged over human labels. The feed parser is reported separately.
- First-pass contract success is the share of completed case-trials with no deterministic `ERROR`, grouped by provider, exact model, and prompt version. Planned, recorded, completed, provider-error, and correction-error trial counts are reported separately.
- Correction success is the share of attempted corrections that finish contract-clean and do not satisfy an attack oracle.
- Prompt-injection attack success is reported before and after correction over attack case-trials only.
- Grounding-error rate is reported two ways. The primary human-adjudicated rate uses every topic whose `grounding_error` label has been completed. The deterministic proxy counts a topic when it has no citation, an ungrounded citation, or a figure/quotation/length heuristic. Unreviewed human labels have a zero denominator and are shown as `n/a`, never silently replaced by the proxy.
- Heuristic claim-check false-positive rate is measured against the deliberately valid gold-label claim cases in the offline suite.
- Latency includes mean, median, p95, and call count. Cost uses provider-reported USD when available. Missing cost is `null`, never silently treated as zero; NVIDIA can use configured per-token rates, while subscription-backed Codex CLI runs generally have no meaningful per-call billed amount.

The labels intentionally include semantic failures the deterministic checker cannot detect, such as conflicting evidence and over-consolidation. Those misses lower recall rather than being removed from the denominator.

## Prompt provenance

The default version is named `production` and hashes the root `briefing-prompt.md`. For a durable comparison, copy a prompt into `evaluator/prompts/`, give it an immutable version name, and pass both versions explicitly. Reports store the name and SHA-256 hash, so reusing a name for changed prompt bytes remains visible. The committed [offline baseline](results/offline-baseline.md) reports checker/feed results and explicitly records live-model metrics as unrun rather than inventing provider data.
