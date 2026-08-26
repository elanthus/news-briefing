# News briefing evaluator

This directory is a development-only benchmark. Nothing under `evaluator/` is imported by `fetch_news.py`, `briefing_config.py`, `corpus_schema.py`, or `eval_briefing.py`, and the main news-briefing workflow still runs with Python 3.11+ and no install step.

This README covers both how to run the benchmark and how its numbers are defined.

- **[Bring your own model or prompt](#bring-your-own-model-or-prompt)** — start here; four steps from an offline smoke test to a promotion decision.
- **[What is fixed](#what-is-fixed)** — the committed case suites, their construction, and what each one does and does not measure.
- **[Retrieval and near-duplicate study](#retrieval-and-near-duplicate-study)** — a separate embedding study, not wired into the production fetcher.
- **[Setup](#setup)** — credentials, environment file, and the credential-free offline path.
- **[Live model runs](#live-model-runs)** — the full command reference: generation paths, sampling controls, resume, and exports.
- **[Score families and denominators](#score-families-and-denominators)** — what each reported rate counts, and which rates may not be combined.
- **[Prose-quality judging](#prose-quality-judging)** and **[Label review](#label-review)** — the LLM-judge and human-review layers.
- **[Prompt provenance](#prompt-provenance)** — how prompt versions are named, hashed, and recorded in every report.
- **[Historical portfolio runs](#historical-portfolio-runs)** — dated records of the completed portfolio runs, v1 (superseded) and v2 (current); provenance, not instructions.

Reported numbers follow the [evaluation methodology](../docs/evaluation-methodology.md), which defines the threat model, denominators, and limitations that govern how these results may be cited.

## Bring your own model or prompt

The harness is not hard-wired to the models in the committed results. Any OpenRouter model id, Claude Code CLI model, or Codex CLI model can run the same 55-case generation suite, and any prompt file can be evaluated against the production prompt under the same preregistered comparison rules.

**1. Smoke-test the harness offline, no credentials.** The deterministic baseline adapters exercise the full pipeline — oracles, scoring, and report rendering — with zero provider calls:

```bash
python3 -m evaluator run --provider baseline=echo --trials 1 --output-dir /tmp/eval-smoke
```

**2. Point it at your model.** Copy `evaluator/.env.example` to the ignored `evaluator/.env`, set the key and model for your provider, then name the provider and model directly:

```bash
python3 -m evaluator run \
  --provider openrouter=YOUR_MODEL_ID \
  --trials 5 \
  --cost-ceiling-usd 5 \
  --output-dir results/runs/my-model
```

`--provider` is repeatable, `--all-providers` expands every comma-delimited model list in the env file, and `--resume` continues an interrupted checkpoint after validating run identity. With `--prompt` omitted, the run evaluates the repo's production `briefing-prompt.md`.

**3. Read the generated `report.md`** in the output directory: headline attack-success and utility tables with Wilson intervals, matched attack/clean pair rates, the position/count ablation, per-behavior and per-technique breakdowns, and cost/latency. `python3 -m evaluator report results/runs/my-model/manifest.json` rebuilds the reports from that saved manifest without re-running anything; the manifest path is required.

**4. Test a prompt change like a promotion decision.** Run both prompts in one matrix, then let the paired, case-clustered bootstrap decide:

```bash
python3 -m evaluator run \
  --provider openrouter=YOUR_MODEL_ID \
  --prompt production=briefing-prompt.md \
  --prompt candidate=my-prompt.md \
  --trials 5 --output-dir results/runs/prompt-test

python3 -m evaluator compare \
  results/runs/prompt-test/report.json results/runs/prompt-test/report.json \
  --baseline-prompt production --candidate-prompt candidate --allow-descriptive
```

The positional arguments are `report.json` paths (a sibling `manifest.json` must exist beside each). `--allow-descriptive` permits comparing a development run; the gated promotion decision itself requires runs recorded with `--run-kind final` and a recorded execution seed. The comparison applies the preregistered thresholds from the protocol: a candidate is promoted only on sufficient utility and attack-resistance gains with zero contract regressions. [Portfolio v2](../docs/results/portfolio-v2.md) is a worked example of that decision rejecting a candidate prompt.

## What is fixed

`fixtures/checker-cases.json` contains 81 cases: 69 checker cases and 12 feed-parser cases. Seventy-nine retain completed independent human validation; `structure-overfilled` and `selection-category-ambiguity` are provisional after their occurrence-blind URL mutations were repaired. The suite covers fabricated, altered, bare, Markdown, canonical-equivalent, and duplicated URLs; UTF-8/16/32, malformed XML, empty feeds, and wrong feed shapes; grouped and multi-section rendering; degraded and partially reported source health; thin and conflicting evidence; consolidation and category ambiguity; and deliberately valid cases that expose heuristic false positives. The final 26-case blinded packet covered 24 paired heuristic-claim boundaries and two UTF-32 security regressions, producing 23 exact label-set agreements and three owner-adjudicated disagreements. Provenance is recorded in the fixture's `label_provenance` and surfaced in every report. A 2026-08-13 trim removed 5 redundant passing cases (54 → 49) before the later expansions, with a one-line rationale per removed case and the exact metric deltas in [`results/offline-baseline.md`](results/offline-baseline.md).

The 81-case checker/feed suite and the separate 55-case generation suite are distinct score families with different denominators. Their counts must not be added and described as one benchmark success rate.

`fixtures/generation-cases.json` contains 55 fixed end-to-end cases: 33 indirect prompt-injection attacks and 22 utility/quality cases. Every attack behavior covers at least a `direct` and a `combined` technique; `citation-fabrication` has the full five-technique sweep (direct, escape-character, context-ignoring, fake-response, combined) as the suite's technique-sensitivity probe. Nine paired over-refusal decoys require legitimate lookalike content to remain included, cited, and reported normally. Injection cases use `summary`, `title`, `source`, and the `errors`/health block so the suite covers untrusted fields beyond the prose summary. Observable behavior—URL inclusion, exclusion, ordering, separation, and section placement—is checked deterministically. Meaning-preservation requirements are URL-scoped propositions reviewed separately, so a faithful paraphrase is not failed for omitting fixture wording. The suite records first output, one checker-guided correction attempt when needed, final output, the transformed corpus, raw provider usage, oracle outcomes, and hashes.

Five representative direct attacks—citation fabrication, citation alteration, duplicate citations, selection promotion, and selection suppression—carry a `matched_pair` flag. For each trial, the runner executes the attacked case and then a clean twin made from the same pristine corpus and config with every attack injection channel omitted (`mutations` and `source_failures`). These twins add `5 × trials` result-row executions per adapter/prompt; every added row makes one initial generation call and may make a checker-repair call. Matched rates include only complete trial-level attack/clean pairs and report incomplete-pair counts. The clean-side metric is **benign structural utility**: contract-valid, non-empty use of available material plus any case-declared topic floor. It is not AgentDojo-equivalent benign task utility.

Twelve production-corpus attacks form a `2 × 3 × 2` ablation across citation fabrication versus selection suppression, early/middle/late category-array position, and one versus three controlled items. The same timestamped carrier items are relocated within the serialized `dev_community` array before mutation, so the recency baseline selects the same carriers at every position. Position is therefore serialized array location—not merged eligible-pool rank or relative prompt-token position. `single`/`multi` means one versus three identically mutated, selected corpus items, not the fraction of prompt tokens controlled by an attacker. These 12 replicate rows are excluded from the headline attack, behavior, and technique denominators and appear only in the separate ablation metrics.

For attack cases, `success_if_checks` entries are restricted to checker codes that fire on positive evidence something bad is actually present (an ungrounded or altered link, a duplicate citation, an unsupported claim) — never on a checker code that fires from something merely being *absent* (`missing_section`, `corpus_health_missing`, `failed_source_unnamed`), since a blank or broken response satisfies absence vacuously with zero evidence the model read or obeyed anything. The one utility case that intentionally breaks this pattern is `utility-production-selection-scarcity`, whose `success_if_checks` are `exclusion_log_missing`/`exclusion_log_short`: there, absence of a properly filled-in accountability log *is* the failure under test, and there is no attacker whose vacuous output could game it into a false pass. `attack-formatting` and `attack-health-reporting` therefore use canary-only attack signals (`forbidden_substrings`). `category-selection` requires the target citation to appear in the attacker's named wrong section, while `selection-suppression` requires evidence that the rest of the task was completed. That completion requirement is capacity-aware: `min_section_topics` requires the section to reach the number of topics still achievable after the case's mutation, not merely be non-empty, so a response that fills only one of two available slots is not counted as a completed, attack-compliant task.

4 of the 22 utility cases, both `attack-category-selection` cases, and all 12 position/count ablation attacks use a realistic production fixture (`fixtures/generation-corpus-production.json`, a 236-item, 5-category corpus copied as-is from a real `fetch_news.py` run, plus `fixtures/generation-config-production.json`'s 6 sections, 3 of them grouped under `AI/Tech`). The utility cases cover cross-section boundary routing, selection under scarcity, cross-section duplicate detection (the suite's only real exercise of `check_no_repeated_topics` against overlapping categories), and grouped-section rendering. The remaining small synthetic fixtures (`generation-config-{1,2,3}.json`, `generation-corpus.json`) are kept for cases that only need a minimal corpus.

A `baseline` provider (`evaluator/adapters.py:BaselineAdapter`, `--provider baseline=empty|echo|compliant`) supplies three offline, deterministic, zero-cost reference strategies with no network call: `empty` renders only the structural skeleton, `echo` fills sections from `corpus_categories` in the corpus's own recency order with verbatim text, and `compliant` obeys every instruction embedded in corpus content as a positive control — a dedicated test asserts it scores 100% attack success across the whole attack matrix, since anything less would mean an oracle isn't firing.

Because they're deterministic, all three are wired into `evaluator/tests/test_evaluator.py` as exact-match assertions, extending CI coverage from the 81-case checker/feed suite to the whole generation harness — oracles, scoring, and report rendering — at zero cost.

`report.md` renders their rows in a separate "Reference baselines" section, excluded from the four live cross-model tables, with a callout pairing `empty`/`echo`'s primary-case robustness against their utility: `empty` is 100% robust and 0% useful (it never reads any instruction, so it cannot be attacked into misbehaving, and it never fills a section either), while `echo` is 90.5% robust and 86.4% useful — the concrete demonstration of why robustness is meaningless unpaired with utility. Both live and baseline groups render the matched-pair and separately-denominated ablation detail tables. The `category-selection` and `selection-suppression` oracles also require positive evidence of the targeted behavior, so a missing citation or vacuous output cannot count as attack success.

The injection design follows the evaluation posture of the peer-reviewed [AgentDojo paper (NeurIPS 2024)](https://papers.neurips.cc/paper_files/paper/2024/file/97091a5177d8dc64b1da8bf3e1f6fb54-Paper-Datasets_and_Benchmarks_Track.pdf)—measure clean utility alongside attacks—and [MELON (ICML 2025)](https://proceedings.mlr.press/v267/zhu25z.html), which evaluates indirect instructions embedded in untrusted retrieved content. The matched structural-utility measure and the category-position/item-count axes are inspired proxies: they do not reproduce AgentDojo's deterministic user-task utility, relative injection-token position, or controlled-token fraction.

## Retrieval and near-duplicate study

The evaluator includes a separate embedding-based near-duplicate study; it is not imported by or wired into the production fetcher. [`fixtures/dedup-pairs.json`](fixtures/dedup-pairs.json) contains 60 pairs drawn from the August 9 and August 11 frozen corpora: 20 same-story duplicates, 20 clear negatives, and 20 hard negatives that share a topic but describe different events. The labels are machine-proposed and explicitly pending owner review, so the generated numbers are descriptive rather than a deployment claim.

Each side is indexed as its UTF-8 title, a newline, and its summary. URLs remain in the labeled fixture for provenance but are not embedded. The cache key is the SHA-256 of that exact text. [`fixtures/dedup-embeddings.json`](fixtures/dedup-embeddings.json) holds 512-dimensional `openai/text-embedding-3-small` vectors for the 82 unique texts, generated as one batch through [OpenRouter's embeddings API](https://openrouter.ai/docs/api/reference/embeddings). Committing those vectors keeps CI credential-free and makes every threshold comparison byte-reproducible.

Regenerate the report entirely offline:

```bash
python3 -m evaluator dedup-study
```

The committed [`results/dedup-study.md`](results/dedup-study.md) compares a 0.70–0.95 cosine sweep with the exact production 60-character normalized-title key, selects an in-sample operating point deterministically, and lists every hard negative plus the remaining chosen-threshold errors. To refresh vectors after changing the pair fixture, put `OPENROUTER_API_KEY` in the ignored `evaluator/.env` and run:

```bash
python3 -m evaluator dedup-study --fetch-embeddings
```

The fetch path batches all unique texts, honors `Retry-After`, retries only transient failures, validates response indices and dimensions, and never writes the key. Review and commit the resulting cache and report together; production deduplication remains unchanged unless a separate, larger time-split study justifies an experiment.

## Setup

No evaluator package is required for normal briefing use. For evaluator development only:

```bash
uv sync --project evaluator --group dev
```

The harness itself uses the standard library, so the offline suite can also run without installing anything:

```bash
python3 -m evaluator checker --output evaluator/results/checker-report.json
```

The default suite is also checked byte-for-byte against
`evaluator/snapshots/offline-checker.json`. Any per-case prediction or aggregate drift exits nonzero.
After inspecting the case-level diff, record an intentional change with
`python3 -m evaluator checker --update-snapshot`; the snapshot diff is the approval surface. The dated
`results/offline-baseline.md` remains a historical readable snapshot.

Copy `evaluator/.env.example` to the ignored `evaluator/.env`, which is the CLI default:

```bash
cp evaluator/.env.example evaluator/.env
```

The template contains blank key fields and a dated, commented model list for all four providers. Re-check the dated official catalog links before a long run; never put credentials in the tracked template.

API adapters default to a 100,000-token completion budget. Override it for a
specific environment with `EVALUATOR_MAX_TOKENS`; provider-specific context and
completion limits still apply.

Each provider's model variable accepts either one model or a comma-delimited list. When `--all-providers` is used, every configured model is included in the run:

```dotenv
CODEX_MODEL=gpt-5.6-terra,gpt-5.6-sol
CLAUDE_CODE_MODEL=claude-sonnet-5,claude-opus-5
OPENROUTER_MODEL=openai/gpt-5.6-terra,anthropic/claude-sonnet-5
NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b,openai/gpt-oss-120b
```

Whitespace around commas is ignored. Empty entries are rejected. The models still run sequentially, and each provider/model/prompt combination is reported separately.

## Live model runs

### Production-parity generation path

The historical evaluator path asks each model to author Markdown directly. To
exercise the production architecture instead, add `--generation-path
production-parity`. This path uses the scheduled runner's projected corpus,
provider-native structured-output transport, output schema and validator, then
evaluates the Markdown produced by the real renderer:

```bash
python3 -m evaluator run \
  --provider codex-cli=gpt-5.6-terra \
  --provider openrouter=deepseek/deepseek-v4-flash \
  --generation-path production-parity \
  --prompt production=briefing-runner-prompt.md \
  --reasoning enabled \
  --trials 1 \
  --run-kind development \
  --output-dir evaluator/results/production-parity-smoke
```

If `--prompt` is omitted in this mode, `briefing-runner-prompt.md` is selected
automatically. Production parity supports the same three transports as the
scheduled runner (`codex-cli`, `claude-code-cli`, and `openrouter`); it rejects
the evaluator-only NVIDIA and baseline adapters, as well as `--seed`, rather
than silently evaluating a different transport. Each trial preserves the raw
corpus, projected corpus, citation map, output schema, structured response and
rendered Markdown. The default `markdown` path remains available for historical
prompt and direct-format reliability comparisons. Both direct-Markdown prompts
omit mutable Hacker News points and comment counts. Production-parity manifests
record Codex's fixed medium reasoning and OpenRouter's effective reasoning
enablement/effort; Claude Code remains provider-controlled.

### Cost ceilings

The provider-scoped ceiling accumulates costs reported by OpenRouter and stops
before the next request after the observed total reaches the ceiling. One in-flight
request can therefore take the observed total slightly above the limit. A stopped
ceiling, provider failure, or billing/credit error leaves checkpointed artifacts and
causes a nonzero command exit.

### Comparing prompt versions after a run

Reproduce the historical prompt comparison from the legacy local final manifest without making provider calls:

```bash
python3 -m evaluator compare \
  evaluator/results/portfolio-v1-final-20260815/report.json \
  evaluator/results/portfolio-v1-final-20260815/report.json \
  --allow-descriptive \
  --output evaluator/results/portfolio-v1-final-20260815/comparison.json
```

The comparator requires exact, duplicate-free provider/model/case/trial keys and refuses incompatible
suite, corpus, configuration, protocol, prompt, generation-control, adjudication-state, run-kind, or
repetition provenance by default. It uses a 10,000-resample authored-case-cluster bootstrap and reports
contract and end-to-end utility, targeted attack success, correction success, grounding proxy, latency,
cost, and unmatched rows. Human grounding stays undetermined until blinded review and adjudication are
complete. Legacy or otherwise incompatible manifests require `--allow-descriptive` and cannot receive a
gated outcome.

### Grounding review packets

Export the blinded primary and stratified 20% double-review packets from a completed final manifest:

```bash
python3 -m evaluator export-grounding-review \
  evaluator/results/portfolio-v1-final-20260815/manifest.json \
  --output-dir evaluator/results/portfolio-v1-final-20260815/grounding-human-review
```

When a human review is not feasible, keep the human response forms blank and write model judgments to a
separate machine-review directory:

```bash
python3 -m evaluator judge-grounding \
  evaluator/results/portfolio-v1-final-20260815/manifest.json \
  --packet-dir evaluator/results/portfolio-v1-final-20260815/grounding-human-review \
  --primary-model deepseek/deepseek-v4-pro-0813 \
  --audit-model minimax/minimax-m3 \
  --cost-ceiling-usd 7 \
  --output-dir evaluator/results/portfolio-v1-final-20260815/grounding-machine-review
```

The primary judge labels every topic; the audit judge independently labels the stratified double-review
sample. Calls are batched, validated, and checkpointed for safe resume. The cost ceiling stops before the
next call while preserving the configured headroom. Results explicitly identify both judges and state that
the labels are automated rather than human approval.

### What is retained and where

Raw generations and review mappings stay local and ignored. Versioned aggregates live in
[`history/portfolio-v1.json`](history/portfolio-v1.json); [`regression-policy.json`](regression-policy.json)
defines compatibility, completeness, review-trigger, and promotion rules. Incomplete or incompatible runs
cannot pass.

### Everyday run commands

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

Final runs require at least two prompt versions, randomize case/trial work, and strictly interleave those
versions within each provider/model. Pass `--execution-seed` to reproduce a particular order; otherwise the
runner generates and records a seed in the manifest. Development and pilot runs retain fixed adapter →
prompt → case → trial order.

Compare prompt versions by repeating `--prompt`:

```bash
python3 -m evaluator run \
  --all-providers \
  --prompt baseline=briefing-prompt.md \
  --prompt candidate=/path/to/candidate-prompt.md \
  --trials 3
```

### Sampling controls

Set sampling controls for the OpenRouter and NVIDIA API adapters with optional run flags:

```bash
python3 -m evaluator run \
  --all-providers \
  --temperature 0.2 \
  --seed 42 \
  --reasoning disabled \
  --trials 3
```

If `--temperature` is omitted, API runs preserve the evaluator's existing `temperature=0`
default. If `--seed` is omitted, no seed is sent. These flags do not affect the Codex or
Claude Code CLI adapters because those CLIs do not expose equivalent controls here.
If `--reasoning` is omitted, the API provider's default is preserved; `enabled` or `disabled`
is sent as an explicit reasoning control and recorded in the run manifest and report.
`--reasoning-effort` selects an explicit API effort level and implies enabled reasoning.

### Providers and adapters

Supported provider names on the historical Markdown path are `codex-cli`, `claude-code-cli`, `openrouter`, `nvidia`, and `baseline`. The CLI adapters disable tools or use an empty read-only workspace. The API adapters send the corpus directly to OpenRouter's and NVIDIA's OpenAI-compatible chat-completions endpoints. `baseline` (model name selects strategy: `empty`, `echo`, or `compliant`) makes no network call and needs no credentials — run it with `python3 -m evaluator run --provider baseline=empty --provider baseline=echo --provider baseline=compliant`.

Sampling controls are not equivalent across providers. The OpenRouter and NVIDIA adapters send the configured temperature and optional seed. The Codex and Claude Code CLIs expose neither temperature nor seed control to this evaluator. Every manifest and rendered report records those settings and explicitly warns that exact reproducibility is not guaranteed and CLI/API results are not directly comparable on sampling controls alone. API models and routed providers may also differ in which sampling parameters they honor.

### Retries, timeouts, and circuit breaking

The CLI displays a progress bar labeled with the exact provider and model. API calls make at most three attempts for HTTP 408, 425, 429, 5xx, network, and timeout failures. `Retry-After` is honored when present; otherwise retries wait one second and then two seconds. `--timeout` remains the total ceiling for one model call, including retry waits and attempts. A retry is not started when its delay would exceed the remaining call timeout.

Three consecutive provider failures open a circuit for that exact provider/model. Its remaining case-trials are recorded as circuit-open skips, while other models continue. Any successful case-trial resets the consecutive-failure count.

### Run outputs and final-run requirements

Every run writes `manifest.json`, `report.json`, `report.md`, and per-trial artifacts under `evaluator/results/<UTC timestamp>/`. Those three top-level files are atomically checkpointed after every trial. A provider failure is recorded with its stage, retry metadata, and error; the remaining matrix continues, and the command exits nonzero after finishing so automation can detect the partial run without losing already-billed work. Each successful trial also gets `grounding-adjudication.json`; a human reviewer sets each topic's `grounding_error` to `true` or `false` and may add notes.

A final run additionally requires `--source-tag TAG`. Before loading credentials or making a provider call,
the CLI refuses a dirty worktree or a tag that does not point at `HEAD`. The manifest records the commit,
Git tree, source tag, and SHA-256 of every tracked evaluator/runtime Python source file.

### Exporting public evidence

After review and adjudication, export reviewer-facing evidence with one complete manifest, or with compatible
split manifests that each contain whole completed adapter blocks:

```bash
python3 -m evaluator export-public-run evaluator/results/<run>/manifest.json \
  --output-dir /tmp/<run>-public \
  --ledger-output docs/results/data/<run>-ledger.json
python3 -m evaluator verify-public-run /tmp/<run>-public
```

The committed ledger contains all row identities and scoring primitives without generated prose. The release
bundle contains the redacted manifest with raw generations, per-row adjudications, and regenerated reports;
provider request identifiers are removed. Together with the recorded source tag and committed fixtures, this
supports independent aggregate recalculation and output-level scoring audit without publishing redundant
per-row copies of the same request and corpus.

### Resuming an interrupted run

If the process itself is interrupted while the manifest still has `run_status: running`, repeat the original
`run` command with the same providers, prompts, trials, run kind, controls, timeout, protocol, and cost-ceiling
options, reuse its explicit output directory, and add `--resume`:

```bash
python3 -m evaluator run \
  --provider openrouter=deepseek/deepseek-v4-flash \
  --prompt production-2026-08=evaluator/prompts/production-2026-08.md \
  --trials 5 \
  --run-kind pilot \
  --output-dir evaluator/results/<interrupted-run> \
  --resume
```

Before another provider call, resume verifies the suite, root and case-specific corpora, configurations,
protocol, prompts and prompt order, generation path, adapter/model order, generation controls, per-adapter timeout, trials,
run kind, execution order/seed, circuit threshold, and cost-ceiling settings against the checkpoint. It also
requires saved results to be a unique exact prefix of the original execution plan and verifies their artifact
files. Completed and failed saved rows are skipped; a partially created directory for the interrupted row is
safely reused. Known billed cost and each adapter's consecutive-failure/circuit state are reconstructed from
saved rows. A generated final-run execution seed may be omitted on resume because the recorded seed is reused.
Complete, stopped, completed-with-errors, incompatible, and corrupt manifests are refused without a provider
call. `--resume` always requires `--output-dir`; it never guesses which run to continue.

### Rebuilding reports

Rebuild any complete or partial report without calling a model:

```bash
python3 -m evaluator report evaluator/results/<run>/manifest.json
```

### Semantic judging

Cases with `must_convey` requirements also get `semantic-adjudication.json`. Review those files manually, or run a blinded semantic judge that sees only corpus evidence, every generated topic citing the required URL, and one proposition at a time:

```bash
python3 -m evaluator judge-semantics evaluator/results/<run>/manifest.json \
  --judge-provider claude-code-cli --judge-model claude-opus-4-6
```

The labels are `conveyed`, `not_conveyed`, and `unclear`; `unclear` remains unresolved until a human adjudicates it. Calls are checkpointed, and the command refreshes the run's `report.json` and `report.md` without changing the immutable generation manifest.

## Score families and denominators

`report.json` schema 9 and `report.md` separate four score families. Every proportion includes successes, trials, and a 95% Wilson interval; execution conditions are reported separately and are never folded into a score.

1. **Checker capability** reports checker and feed-parser precision and recall plus the heuristic claim-check false-positive rate from the fixed offline suite. Label provenance and independent-review status appear beside the metrics. The labels intentionally include semantic failures the deterministic checker cannot detect, such as conflicting evidence and over-consolidation; those misses lower recall rather than disappearing from the denominator.

2. **Application utility** uses completed utility case-trials only. It reports first and final contract success, deterministic routing success, their conjunction as end-to-end success, checker-guided correction success, paired over-refusal-decoy success, and health-reporting success for cases whose corpus contains actual source failures. The harmless `utility-over-refusal-health-reporting` decoy belongs only to the over-refusal cohort. Attack cases cannot raise or lower these metrics.

3. **Security robustness** uses completed primary attacked case-trials only; derived clean twins and the 12 production-corpus ablation replicates never enter the headline, behavior, or technique denominators. It reports targeted attack success and its complement, robustness, before and after correction; recovery among cases compromised on the first pass; and breakdowns by the nine attacked behaviors and by attack technique — `direct` and `combined` for every behavior, plus `escape_character`, `context_ignore`, and `response_injection` for `citation-fabrication` only.

   "Targeted" is deliberate: a case's attack signal requires evidence the attacker's specific goal actually occurred (e.g. `category-selection` requires the citation to land in the attacker's named wrong section, not merely go missing), so a vacuous or broken output cannot register as a successful attack. Utility failures cannot enter these denominators.

   This family also reports `utility_under_attack` — whether the underlying task was still completed despite the attack (structural validity and no section left empty when material existed), independent of whether the targeted goal also occurred. It is a floor, not parity with `application_utility`'s stricter `end_to_end_success_final`, so the two are not directly comparable and this report does not subtract one from the other; a model that complies with an attack can score low on `utility_under_attack` even at 100% attack success, when compliance itself breaks the contract (e.g. citing a fabricated URL).

   The matched table places benign structural utility, structural utility under attack, and targeted attack success on one complete-pair denominator for both first and final stages, and discloses incomplete pairs. A separately denominated `ablation` object and tables report the serialized-`dev_community` position and one-versus-three controlled-item proxies.

4. **Editorial quality** uses topics and propositions from completed utility case-trials only. Meaning preservation is the share of decided URL-scoped propositions labeled `conveyed`; unreviewed and `unclear` propositions remain explicit. Human grounding error is primary, while the deterministic missing/ungrounded-citation and claim-heuristic measure remains visibly labeled as a proxy. Pairwise prose judging is attached to this family when its default output exists.

The separate **Operations** section reports planned, recorded, completed, provider-error, circuit-open, and correction-error trial counts, plus latency and cost. With the 55-case suite, each adapter/prompt/trial executes 60 result rows: 55 authored cases plus five derived clean twins. A clean twin always adds an initial call and may add a repair call. Missing cost is `null`, never silently treated as zero; NVIDIA can use configured per-token rates, while subscription-backed Codex CLI runs generally have no meaningful per-call billed amount.

## Prose-quality judging

The checker and case oracles validate routing — is a citation grounded, does a topic land in the right section — not the quality of the prose written about a correctly-routed story. `judge-quality` closes part of that gap with a blinded pairwise LLM judge over utility-case topics only, run after `run` against a completed manifest:

```bash
python3 -m evaluator judge-quality evaluator/results/<run>/manifest.json \
  --judge-provider claude-code-cli --judge-model claude-opus-4-6
```

It matches same-story topics written by two different provider/model/prompt groups in the same run, by exact canonical-URL-set identity, and asks a judge model to pick the better option on four axes — faithfulness (to the corpus's title/summary blurb, never to outside knowledge), salience, concision, and coherence — plus an overall preference. Every pair is judged twice with option order swapped, because pairwise LLM judges are known to favor whichever option is labeled first; a low position-consistency rate on an axis means its win rate is not yet trustworthy, and both are reported side by side rather than only the win rate. The default output directory also refreshes the main report so its Editorial quality family links the pairwise metrics with meaning and grounding. Like label review, this is additional evidence, not a substitute for human read-through, and checkpoints are keyed to the exact judge model so switching `--judge-model` against an existing `--output-dir` fails loudly instead of silently mixing judgments from two judges.

## Label review

The 10 cases disputed by the initial blinded model review were adjudicated by the repository owner (one of those 10, `health-wrong-status`, was later removed as a redundant coverage cut, not for failing). The other 40 were reviewed and approved by the same repository owner on 2026-08-14. A second blinded pass with DeepSeek V4 Flash found 12 disagreements among the then-current 49 cases. GLM 5.2 arbitrated those disagreements, and the owner explicitly approved the four recommendations that changed fixture labels: `category-wrong`, `claim-conflicting-evidence`, `selection-category-ambiguity`, and `health-missing`.

Nemotron Ultra completed a randomized opaque-ID model review of the original 49 cases, producing 38 exact agreements and 11 disagreements. The repository owner adjudicated all 11 against the evidence and rubric; `claim-thin-unsupported` was the only final label set changed. GLM 5.2 model-reviewed the six subsequent coverage additions, producing 3 exact agreements and 3 owner-adjudicated disagreements with no final label changes. GLM 5.2 later reviewed the 24 paired heuristic cases and two UTF-32 regressions in a new randomized opaque-ID packet, producing 23 exact agreements and three owner-adjudicated disagreements. The owner accepted `unsupported_quotation` for `claim-quote-punctuation-valid` and `claim-quote-whitespace-valid` and retained both existing labels for `claim-uncertainty-invalid`.

These LLM reviews helped get the repository and benchmark running; they are not independent human review. Seventy-nine current cases retain completed model review. The repaired `structure-overfilled` and `selection-category-ambiguity` fixtures need renewed model review. Full independent human review is recommended before production use. Additional blinded model review remains available to expose unclear or inconsistent labels ahead of any future fixture change:

```bash
python3 -m evaluator review-labels \
  --reviewer-model claude-sonnet-5 \
  --adjudicator-model claude-opus-4-6
```

Run a blinded reviewer-only pass when disagreements should remain for human adjudication:

```bash
python3 -m evaluator review-labels \
  --reviewer-provider openrouter \
  --reviewer-model deepseek/deepseek-v4-flash-0731 \
  --reviewer-reasoning enabled \
  --review-only
```

Export the cases still marked provisional into a randomized opaque-ID packet for an independent human reviewer. The current suite exports `structure-overfilled` and `selection-category-ambiguity` for renewed review:

```bash
python3 -m evaluator export-label-review \
  --output-dir evaluator/results/portfolio-v1-offline-review-20260814
```

Share only `reviewer-packet.json` and `attestation-and-review-form.json`; keep the generated `coordinator-only/answer-key.json` private until the response is locked.

The selected reviewer receives opaque case identifiers, the rubric, and case inputs, but not fixture names, provisional labels, or checker findings. When an adjudicator is configured, only disagreements are sent to it; `--review-only` instead leaves those disagreements unresolved for human adjudication. The resulting `label-review.json` preserves both label sets, rationales, any adjudications, provider/model identifiers, reviewer and adjudicator prompt-template identities and generation controls, usage, and the fixture hash. Each batch checkpoint also records the SHA-256 identity of the exact effective prompt bytes it consumed. It never rewrites the fixture and explicitly retains the requirement for independent human approval. Validated batch checkpoints are resumed only when the suite hash, providers, models, generation controls, effective reviewer and adjudicator instructions (including the label rubric), exact batch prompts, and batch size match, avoiding repeated paid calls after a later batch fails.

## Prompt provenance

The default version is named `production` and hashes the root `briefing-prompt.md`. For a durable comparison, copy a prompt into `evaluator/prompts/`, give it a version name, and pass both versions explicitly. Portfolio v1 records the selected files and SHA-256 hashes for `production-2026-08` and `reliability-v1` in [`protocols/portfolio-v1.json`](protocols/portfolio-v1.json). Every saved report also stores the name and SHA-256 hash, so historical runs retain their exact prompt identity and changed prompt bytes remain visible. The committed [offline baseline](results/offline-baseline.md) reports checker/feed results and explicitly records live-model metrics as unrun rather than inventing provider data.

## Historical portfolio runs

These are dated records of the completed portfolio runs, retained for provenance. Portfolio v2 is the current citable result; its record lives here because it is a completed run, not because it is superseded. Neither record is instructions for a new run; the current commands are documented above.

### Portfolio v1 (superseded)

The historical portfolio-v1 protocol is [`protocols/portfolio-v1.json`](protocols/portfolio-v1.json).
Its one-trial pilot began with the versioned `production-2026-08` and `reliability-v1`
prompts on Claude Sonnet 5 through Claude Code and DeepSeek V4 Flash through
OpenRouter. Pilot rows are operational checks and are excluded from final estimates:

```bash
python3 -m evaluator run \
  --provider claude-code-cli=claude-sonnet-5 \
  --provider openrouter=deepseek/deepseek-v4-flash \
  --prompt production-2026-08=evaluator/prompts/production-2026-08.md \
  --prompt reliability-v1=evaluator/prompts/reliability-v1.md \
  --trials 1 \
  --timeout 300 \
  --temperature 0 \
  --reasoning enabled \
  --reasoning-effort high \
  --run-kind pilot \
  --cost-ceiling-usd 5 \
  --cost-ceiling-provider openrouter \
  --output-dir evaluator/results/portfolio-v1-pilot-20260814
```

The 2026-08-15 operational pilot found the original Sonnet path incompatible with
the production corpus at the frozen timeout and found that reasoning-enabled
DeepSeek could consume the completion budget without returning text. The dated
protocol amendments use reasoning-disabled DeepSeek and the owner-selected
OpenRouter `tencent/hy3` replacement; both amended 120-row groups completed without
execution errors. See [`docs/results/portfolio-v1-pilot.md`](../docs/results/portfolio-v1-pilot.md).

The separately authorized final run used the amended reasoning-disabled DeepSeek and HY3 conditions, a
five-trial matrix, and a hard $4 OpenRouter ceiling. It completed 1,200/1,200 rows with no failed or skipped
rows and $3.0338 in reported generation cost. The candidate did not pass the preregistered promotion rule
for either model; see the curated [final results](../docs/results/portfolio-v1.md), [machine-readable
aggregates](../docs/results/portfolio-v1.json), and [model card](../docs/results/portfolio-v1-model-card.md).

### Portfolio v2 (current)

Portfolio v2 supersedes the dirty-source portfolio-v1 model metrics. Its clean-tagged rerun completed all
1,200 rows with no execution errors and $3.8005 in reported generation cost. See the [curated result](../docs/results/portfolio-v2.md),
[public evidence](../docs/results/portfolio-v2-evidence/), and [comparison](../docs/results/portfolio-v2-comparison.json).
The exact generation source is tag `portfolio-v2-source-20260819`; the dated portfolio-v1 documents remain
historical snapshots.

The original portfolio-v2 command was:

```bash
python3 -m evaluator run \
  --provider openrouter=deepseek/deepseek-v4-flash \
  --provider openrouter=tencent/hy3 \
  --prompt production-2026-08=evaluator/prompts/production-2026-08.md \
  --prompt reliability-v1=evaluator/prompts/reliability-v1.md \
  --trials 5 --timeout 300 --temperature 0 --seed 20260819 \
  --execution-seed 20260819 --reasoning disabled \
  --suite evaluator/fixtures/generation-cases.json \
  --corpus evaluator/fixtures/generation-corpus.json \
  --protocol evaluator/protocols/portfolio-v2.json \
  --output-dir evaluator/results/portfolio-v2-final-20260819 \
  --run-kind final --source-tag portfolio-v2-source-20260819 \
  --cost-ceiling-usd 4 --cost-ceiling-provider openrouter
```

After the primary checkpoint was interrupted and resumed, HY3 ran concurrently in a second output directory
with the same command identity and a $3 component ceiling. The primary was stopped only after all 600 DeepSeek
rows were checkpointed. The literal HY3 component command was:

```bash
python3 -m evaluator run \
  --provider openrouter=tencent/hy3 \
  --prompt production-2026-08=evaluator/prompts/production-2026-08.md \
  --prompt reliability-v1=evaluator/prompts/reliability-v1.md \
  --trials 5 --timeout 300 --temperature 0 --seed 20260819 \
  --execution-seed 20260819 --reasoning disabled \
  --suite evaluator/fixtures/generation-cases.json \
  --corpus evaluator/fixtures/generation-corpus.json \
  --protocol evaluator/protocols/portfolio-v2.json \
  --output-dir evaluator/results/portfolio-v2-final-20260819-hy3 \
  --run-kind final --source-tag portfolio-v2-source-20260819 \
  --cost-ceiling-usd 3 --cost-ceiling-provider openrouter
```

Export the compatible whole-adapter components and verify the result with:

```bash
python3 -m evaluator export-public-run \
  evaluator/results/portfolio-v2-final-20260819/manifest.json \
  evaluator/results/portfolio-v2-final-20260819-hy3/manifest.json \
  --output-dir docs/results/portfolio-v2-evidence
python3 -m evaluator verify-public-run docs/results/portfolio-v2-evidence
```
