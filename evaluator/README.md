# News briefing evaluator

This directory is a development-only benchmark. Nothing under `evaluator/` is imported by `fetch_news.py`, `briefing_config.py`, `corpus_schema.py`, or `eval_briefing.py`, and the main news-briefing workflow still runs with Python 3.11+ and no install step.

## What is fixed

`fixtures/checker-cases.json` contains 54 fixed, human-reviewable gold-label cases: 42 checker cases and 12 feed-parser cases. They cover fabricated, altered, bare, Markdown, canonical-equivalent, and duplicated URLs; UTF-8/16/32, malformed XML, empty feeds, and wrong feed shapes; degraded and partially reported source health; thin and conflicting evidence; consolidation and category ambiguity; and deliberately valid cases that expose false positives. The committed labels are provisional until an independent human approves them; their provenance and review status are part of the suite and every report.

`fixtures/generation-cases.json` contains 63 fixed end-to-end cases: 45 indirect prompt-injection attacks and 18 utility/quality cases. Each of the nine attack behaviors covers direct, escape-character, context-ignoring, fake-response, and combined techniques with shared assertions. Nine paired over-refusal decoys require legitimate lookalike content to remain included, cited, and reported normally. Focused utility cases require the mutated item to be selected and apply case-specific assertions for evidence conflicts, section placement, and over-consolidation. Selection attacks assert URL inclusion, exclusion, lead position, and section placement rather than relying only on marker strings. The suite records first output, one correction attempt when needed, final output, the transformed corpus, raw provider usage, oracle outcomes, and hashes.

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

Each provider's model variable accepts either one model or a comma-delimited list. When `--all-providers` is used, every configured model is included in the run:

```dotenv
CODEX_MODEL=gpt-5.6-terra,gpt-5.6-sol
CLAUDE_CODE_MODEL=claude-sonnet-5,claude-opus-5
OPENROUTER_MODEL=openai/gpt-5.6-terra,anthropic/claude-sonnet-5
NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b,openai/gpt-oss-120b
```

Whitespace around commas is ignored. Empty entries are rejected. The models still run sequentially, and each provider/model/prompt combination is reported separately.

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

Set sampling controls for the OpenRouter and NVIDIA API adapters with optional run flags:

```bash
python3 -m evaluator run \
  --all-providers \
  --temperature 0.2 \
  --seed 42 \
  --trials 3
```

If `--temperature` is omitted, API runs preserve the evaluator's existing `temperature=0`
default. If `--seed` is omitted, no seed is sent. These flags do not affect the Codex or
Claude Code CLI adapters because those CLIs do not expose equivalent controls here.

Supported provider names are `codex-cli`, `claude-code-cli`, `openrouter`, and `nvidia`. The CLI adapters disable tools or use an empty read-only workspace. The API adapters send the corpus directly to OpenRouter's and NVIDIA's OpenAI-compatible chat-completions endpoints.

Sampling controls are not equivalent across providers. The OpenRouter and NVIDIA adapters send the configured temperature and optional seed. The Codex and Claude Code CLIs expose neither temperature nor seed control to this evaluator. Every manifest and rendered report records those settings and explicitly warns that exact reproducibility is not guaranteed and CLI/API results are not directly comparable on sampling controls alone. API models and routed providers may also differ in which sampling parameters they honor.

The CLI displays a progress bar labeled with the exact provider and model. API calls make at most three attempts for HTTP 408, 425, 429, 5xx, network, and timeout failures. `Retry-After` is honored when present; otherwise retries wait one second and then two seconds. `--timeout` remains the total ceiling for one model call, including retry waits and attempts. A retry is not started when its delay would exceed the remaining call timeout.

Three consecutive provider failures open a circuit for that exact provider/model. Its remaining case-trials are recorded as circuit-open skips, while other models continue. Any successful case-trial resets the consecutive-failure count.

Every run writes `manifest.json`, `report.json`, `report.md`, and per-trial artifacts under `evaluator/results/<UTC timestamp>/`. Those three top-level files are atomically checkpointed after every trial. A provider failure is recorded with its stage, retry metadata, and error; the remaining matrix continues, and the command exits nonzero after finishing so automation can detect the partial run without losing already-billed work. Each successful trial also gets `grounding-adjudication.json`; a human reviewer sets each topic's `grounding_error` to `true` or `false` and may add notes. Rebuild any complete or partial report without calling a model:

```bash
python3 -m evaluator report evaluator/results/<run>/manifest.json
```

## Metrics and denominators

Every proportion includes successes, trials, and a 95% Wilson interval.

- Checker precision and recall are micro-averaged over human labels. The feed parser is reported separately.
- First-pass contract success is the share of completed case-trials with no deterministic `ERROR`, grouped by provider, exact model, and prompt version. Planned, recorded, completed, provider-error, and correction-error trial counts are reported separately.
- Utility-oracle success is the share of utility case-trials satisfying their case-specific content, URL-selection, separation, and placement assertions. It complements rather than replaces contract success; semantic quality outside those explicit assertions still requires human review.
- Correction success is the share of attempted corrections that finish contract-clean and satisfy the case's utility or attack assertions.
- Prompt-injection attack success is reported before and after correction over attack case-trials only.
- Grounding-error rate is reported two ways. The primary human-adjudicated rate uses every topic whose `grounding_error` label has been completed. The deterministic proxy counts a topic when it has no citation, an ungrounded citation, or a figure/quotation/length heuristic. Unreviewed human labels have a zero denominator and are shown as `n/a`, never silently replaced by the proxy.
- Heuristic claim-check false-positive rate is measured against the deliberately valid gold-label claim cases in the offline suite.
- Latency includes mean, median, p95, and call count. Cost uses provider-reported USD when available. Missing cost is `null`, never silently treated as zero; NVIDIA can use configured per-token rates, while subscription-backed Codex CLI runs generally have no meaningful per-call billed amount.

The labels intentionally include semantic failures the deterministic checker cannot detect, such as conflicting evidence and over-consolidation. Those misses lower recall rather than being removed from the denominator.

## Independent label review

The 10 cases disputed by the initial blinded model review were adjudicated by the repository owner; the other 44 remain provisional until independently reviewed by a human. Additional blinded model review can expose unclear or inconsistent labels without being represented as human approval:

```bash
python3 -m evaluator review-labels \
  --reviewer-model claude-sonnet-5 \
  --adjudicator-model claude-opus-4-6
```

Sonnet receives opaque case identifiers, the rubric, and case inputs, but not fixture names, provisional labels, or checker findings. Only disagreements are sent to Opus. The resulting `label-review.json` preserves both label sets, rationales, adjudications, model identifiers, usage, and the fixture hash. It never rewrites the fixture and explicitly retains the requirement for independent human approval. Validated batch checkpoints are resumed only when the suite hash, models, and batch size match, avoiding repeated paid calls after a later batch fails.

## Prompt provenance

The default version is named `production` and hashes the root `briefing-prompt.md`. For a durable comparison, copy a prompt into `evaluator/prompts/`, give it an immutable version name, and pass both versions explicitly. Reports store the name and SHA-256 hash, so reusing a name for changed prompt bytes remains visible. The committed [offline baseline](results/offline-baseline.md) reports checker/feed results and explicitly records live-model metrics as unrun rather than inventing provider data.
