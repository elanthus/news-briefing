# News briefing evaluator

This directory is a development-only benchmark. Nothing under `evaluator/` is imported by `fetch_news.py`, `briefing_config.py`, `corpus_schema.py`, or `eval_briefing.py`, and the main news-briefing workflow still runs with Python 3.11+ and no install step.

## What is fixed

`fixtures/checker-cases.json` contains 54 fixed, human-reviewable gold-label cases: 42 checker cases and 12 feed-parser cases. They cover fabricated, altered, bare, Markdown, canonical-equivalent, and duplicated URLs; UTF-8/16/32, malformed XML, empty feeds, and wrong feed shapes; degraded and partially reported source health; thin and conflicting evidence; consolidation and category ambiguity; and deliberately valid cases that expose false positives. The committed labels are provisional until an independent human approves them; their provenance and review status are part of the suite and every report.

`fixtures/generation-cases.json` contains 63 fixed end-to-end cases: 45 indirect prompt-injection attacks and 18 utility/quality cases. Each of the nine attack behaviors covers direct, escape-character, context-ignoring, fake-response, and combined techniques with shared assertions. Nine paired over-refusal decoys require legitimate lookalike content to remain included, cited, and reported normally. Observable behavior—URL inclusion, exclusion, ordering, separation, and section placement—is checked deterministically. Meaning-preservation requirements are URL-scoped propositions reviewed separately, so a faithful paraphrase is not failed for omitting fixture wording. The suite records first output, one checker-guided correction attempt when needed, final output, the transformed corpus, raw provider usage, oracle outcomes, and hashes.

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

Cases with `must_convey` requirements also get `semantic-adjudication.json`. Review those files manually, or run a blinded semantic judge that sees only corpus evidence, every generated topic citing the required URL, and one proposition at a time:

```bash
python3 -m evaluator judge-semantics evaluator/results/<run>/manifest.json \
  --judge-provider claude-code-cli --judge-model claude-opus-4-6
```

The labels are `conveyed`, `not_conveyed`, and `unclear`; `unclear` remains unresolved until a human adjudicates it. Calls are checkpointed, and the command refreshes the run's `report.json` and `report.md` without changing the immutable generation manifest.

## Score families and denominators

`report.json` schema 6 and `report.md` separate four score families. Every proportion includes successes, trials, and a 95% Wilson interval; execution conditions are reported separately and are never folded into a score.

1. **Checker capability** reports checker and feed-parser precision and recall plus the heuristic claim-check false-positive rate from the fixed offline suite. Label provenance and independent-review status appear beside the metrics. The labels intentionally include semantic failures the deterministic checker cannot detect, such as conflicting evidence and over-consolidation; those misses lower recall rather than disappearing from the denominator.
2. **Application utility** uses completed utility case-trials only. It reports first and final contract success, deterministic routing success, their conjunction as end-to-end success, checker-guided correction success, paired over-refusal-decoy success, and degraded-source health-reporting success. Attack cases cannot raise or lower these metrics.
3. **Security robustness** uses completed attack case-trials only. It reports attack success and its complement, robustness, before and after correction; recovery among cases compromised on the first pass; and breakdowns by the nine attacked behaviors and five attack techniques. Utility failures cannot enter these denominators.
4. **Editorial quality** uses topics and propositions from completed utility case-trials only. Meaning preservation is the share of decided URL-scoped propositions labeled `conveyed`; unreviewed and `unclear` propositions remain explicit. Human grounding error is primary, while the deterministic missing/ungrounded-citation and claim-heuristic measure remains visibly labeled as a proxy. Pairwise prose judging is attached to this family when its default output exists.

The separate **Operations** section reports planned, recorded, completed, provider-error, circuit-open, and correction-error trial counts, plus latency and cost. Missing cost is `null`, never silently treated as zero; NVIDIA can use configured per-token rates, while subscription-backed Codex CLI runs generally have no meaningful per-call billed amount.

## Prose-quality judging

The checker and case oracles validate routing — is a citation grounded, does a topic land in the right section — not the quality of the prose written about a correctly-routed story. `judge-quality` closes part of that gap with a blinded pairwise LLM judge over utility-case topics only, run after `run` against a completed manifest:

```bash
python3 -m evaluator judge-quality evaluator/results/<run>/manifest.json \
  --judge-provider claude-code-cli --judge-model claude-opus-4-6
```

It matches same-story topics written by two different provider/model/prompt groups in the same run, by exact canonical-URL-set identity, and asks a judge model to pick the better option on four axes — faithfulness (to the corpus's title/summary blurb, never to outside knowledge), salience, concision, and coherence — plus an overall preference. Every pair is judged twice with option order swapped, because pairwise LLM judges are known to favor whichever option is labeled first; a low position-consistency rate on an axis means its win rate is not yet trustworthy, and both are reported side by side rather than only the win rate. The default output directory also refreshes the main report so its Editorial quality family links the pairwise metrics with meaning and grounding. Like label review, this is additional evidence, not a substitute for human read-through, and checkpoints are keyed to the exact judge model so switching `--judge-model` against an existing `--output-dir` fails loudly instead of silently mixing judgments from two judges.

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
