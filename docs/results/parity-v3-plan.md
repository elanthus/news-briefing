# Parity v3 plan

Status: planned, not run. No model call has been made for this plan. Protocol:
[`parity-v3.json`](../../evaluator/protocols/parity-v3.json) (status
`draft_pending_freeze`). Read the [evaluation methodology](../evaluation-methodology.md)
before quoting any number this run eventually produces.

## Goal

Measure generation on the production-parity two-pass path at the current source
revision, with the same matrix as [Parity v2](parity-v2.md): two OpenRouter models
(`deepseek/deepseek-v4-flash`, `tencent/hy3`), two prompt versions
(`production-runner`, `runner-deepseek-v4-flash`), the version 8 generation suite,
five trials per case, temperature 0, disabled reasoning, 300-second timeout, final
run kind, execution seed `20260901`, and a clean source tag. Parity v2 is the
only citable production-path record and predates the version 9 oracle changes,
the publication-provenance remediation, and the per-slot repair work; Parity v3
gives the repository a current-revision measurement to set beside it.

## Baseline

The baseline is the frozen Parity v2 public bundle,
[`parity-v2-evidence/`](parity-v2-evidence/), fetched and verified with:

```bash
python3 -S -m evaluator.evidence_assets fetch
python3 -S -m evaluator verify-public-run .news-briefing/evidence/parity-v2-evidence
```

Its identity is tag `parity-v2-source-20260904`, commit `87db491c`, protocol
[`parity-v1.json`](../../evaluator/protocols/parity-v1.json), 1,200 planned rows,
1,198 completed, two DeepSeek rows retained as `provider_error`. Portfolio v1/v2,
Parity v1, the August comparisons, raw checkpoint trees, and GitHub artifacts are
archive only and are not inputs to this run or its comparison.

## What is and is not pinned

The protocol pins the parity-v2 models, controls, prompt version names, suite
version, trial count, run kind, and tag convention. It cannot pin the parity-v2
bytes. Since the parity-v2 tag, the version 8 suite file, both corpus fixtures
(schema v7 metadata refresh), and `briefing-runner-prompt.md` have changed, and
any new protocol file has its own SHA-256. [`regression-policy.json`](../../evaluator/regression-policy.json)
requires `same_suite_sha256`, `same_corpus_sha256`, `same_protocol_sha256`, and
`shared_prompt_versions_have_same_sha256`, so the comparator will classify
Parity v3 against Parity v2 as `not_gate_eligible_descriptive_comparison`,
exactly as it classified Parity v2 against Parity v1. The policy says so itself:
incompatible comparisons may be descriptive but cannot pass.

Two comparisons under this plan are gate-eligible:

- The within-run prompt comparison, `production-runner` (baseline) versus
  `runner-deepseek-v4-flash` (candidate), on the Parity v3 manifest alone. It
  satisfies every compatibility field by construction, provided the run is
  `final`, has a recorded seed, and completes with zero failed or skipped rows.
- Any later rerun that reuses the frozen `parity-v3.json`, suite, corpus,
  configuration, and prompt bytes. Parity v3 then becomes that run's baseline.

## Before the run

1. Confirm the fixture and prompt hashes in `parity-v3.json` still match the
   committed bytes (`python3 -S -m unittest evaluator.tests.test_protocols`).
2. Set `frozen_at`, `authorization.authorized_at`, and `required_tag`
   (`parity-v3-source-YYYYMMDD`), change `status` to `frozen_for_run`, commit.
3. Tag the clean HEAD with that name. `evaluator run --run-kind final` refuses a
   dirty worktree or a tag that does not point at HEAD.
4. Copy `evaluator/.env.example` to the ignored `evaluator/.env` with
   `OPENROUTER_API_KEY`. No other credential is needed.

## Run command

One matrix, both models, both prompts, in one output directory. Parity v2 was split
across two component checkpoints after an interruption; a single component avoids the
public-export merge step.

```bash
python3 -m evaluator run \
  --provider openrouter=deepseek/deepseek-v4-flash \
  --provider openrouter=tencent/hy3 \
  --prompt production-runner=briefing-runner-prompt.md \
  --prompt runner-deepseek-v4-flash=evaluator/prompts/briefing-runner-prompt-deepseek-v4-flash.md \
  --suite evaluator/fixtures/generation-cases.json \
  --protocol evaluator/protocols/parity-v3.json \
  --trials 5 \
  --temperature 0 \
  --reasoning disabled \
  --timeout 300 \
  --execution-seed 20260901 \
  --run-kind final \
  --source-tag parity-v3-source-YYYYMMDD \
  --cost-ceiling-usd 5 \
  --cost-ceiling-provider openrouter \
  --output-dir evaluator/results/parity-v3-final-YYYYMMDD
```

If the process is interrupted while the manifest is `running`, repeat the same
command with `--resume`. Provider errors do not stop the matrix; the command exits
nonzero after finishing so the partial run is visible.

## Compare and export commands

Within-run prompt comparison (gate-eligible when the run completes cleanly):

```bash
python3 -m evaluator compare \
  evaluator/results/parity-v3-final-YYYYMMDD/report.json \
  evaluator/results/parity-v3-final-YYYYMMDD/report.json \
  --baseline-prompt production-runner --candidate-prompt runner-deepseek-v4-flash \
  --output docs/results/parity-v3-comparison-prompts.json
```

Descriptive comparison against the Parity v2 bundle, one per shared prompt version:

```bash
python3 -m evaluator compare \
  .news-briefing/evidence/parity-v2-evidence/manifest.json \
  evaluator/results/parity-v3-final-YYYYMMDD/report.json \
  --baseline-prompt production-runner --candidate-prompt production-runner \
  --allow-descriptive \
  --output docs/results/parity-v3-comparison-parity-v2-production-runner.json
```

Family breakdown and public export, as for Parity v2:

```bash
python3 -S -m evaluator.family_rates evaluator/results/parity-v3-final-YYYYMMDD/ledger.json
python3 -m evaluator export-public-run evaluator/results/parity-v3-final-YYYYMMDD/manifest.json \
  --output-dir docs/results/parity-v3-evidence \
  --ledger-output docs/results/parity-v3-evidence/ledger.json
python3 -m evaluator verify-public-run docs/results/parity-v3-evidence
```

Large bundle files go to a release asset listed in [EVIDENCE-ASSETS.md](EVIDENCE-ASSETS.md),
following the existing bundles.

## Expected cost

Parity v2's two components reported $1.863904276212 in generation cost for 1,200
rows across 2,461 calls, under a $5 DeepSeek ceiling and a $3 HY3 ceiling. Parity v3
is the same matrix on the same providers, so the planning figure is roughly $2 with a
$5 ceiling, as the protocol's `authorization` block records. Provider prices can
change; the manifest records observed billed cost and counts unknown-cost calls.

## Pass criteria

The policy is read from `regression-policy.json`; the comparison output records the
policy file's SHA-256 (`bf9ed083…c629ce46` at this revision).

- **Completion.** 1,200/1,200 planned rows completed, zero failed, zero skipped, no
  billing or provider failure. A `completed_with_errors` run is reportable but
  cannot pass.
- **Within-run prompt gate.** `runner-deepseek-v4-flash` passes against
  `production-runner` only if final end-to-end utility improves by at least +5.0
  points, the upper bound of the paired authored-case-cluster bootstrap interval
  for the attack-success delta is below +5.0 points, there are zero new
  deterministic contract regressions, and human grounding error does not increase.
  Human grounding must be complete for a pass; with no human review the outcome
  remains `undetermined` on that rule and the comparison cannot pass.
- **Against Parity v2.** Descriptive only. Report the paired deltas with their
  clustered intervals and the comparator's outcome string; do not report a pass.

## Claims boundary

The result may claim: structural utility and targeted attack success on the fixed
55-case version 8 suite at the recorded revision, with Wilson intervals and
case-level intervals; descriptive deltas against Parity v2 with their recorded
incompatibility reasons; family and ablation breakdowns reproducible from the ledger.

The result may not claim: news quality, factual grounding, or meaning
preservation (no human semantic or grounding review is in scope); a promotion or
regression decision against Parity v2; daily-service reliability (the benchmark
uses temperature 0, disabled reasoning, one correction, and a frozen tag, while the
daily runner uses temperature 0.2, enabled reasoning, a fallback chain, and up to
three corrections per stage); results under version 9 oracles; or generalization
beyond the authored suite. HY3 answers a weaker provider-enforced citation
contract (`uniqueItems` stripped), so its cells are not comparable with DeepSeek's.

Once Parity v3 runs, Parity v2 becomes the prior baseline in the methodology and
README tables, and remains citable as a frozen historical experiment.
