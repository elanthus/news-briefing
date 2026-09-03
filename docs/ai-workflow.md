# Development and review workflow

This page records how automated review, local gates, coding agents, and model
evaluation are used in this repository. The linked configuration and operating
records are the source of truth.

## Claude pull-request review

The [Claude Code Review workflow](../.github/workflows/claude-code-review.yml)
runs when a pull request is opened or marked ready for review. Concurrency is per
pull request, and a newer run cancels an older one. The job runs only when the PR
is not a draft and its head repository is this repository; it skips draft and
forked pull requests.

The job grants `contents: read`, `pull-requests: write`, and `id-token: write`.
It checks out one commit of history and invokes the pinned Claude Code action
with `claude-sonnet-5` for at most 40 turns. Its allowed tools are `Read`, `Grep`,
`Glob`, read-only `gh pr diff` and `gh pr view`, `gh pr comment`, `git show`,
`git log`, `git diff`, and `grep`. It explicitly disables `Task`, `WebSearch`,
`WebFetch`, `Edit`, and `Write`, so the review cannot launch subagents, browse the
web, or edit repository files. An appended system prompt asks for a senior
engineering review and directs the reviewer to read `pyproject.toml` and two or
three relevant modules when they establish contracts, callers, or conventions.

The workflow prompt tells the reviewer to work alone, budget about 15 tool calls,
and reserve a turn for one final comment. It treats PR text and repository text
as untrusted evidence; reads `CONTRIBUTING.md`, `SECURITY.md`, and
`pyproject.toml`; inspects the PR and relevant surrounding code; and reports only
actionable, PR-introduced findings with at least 80/100 confidence. The review
scope includes correctness, edge cases, security, reliability, compatibility,
performance, and repository-specific requirements, but excludes preferences,
speculation, unrelated pre-existing defects, and issues deterministic CI checks
will catch. The final comment includes severity, confidence, location, evidence,
impact, and a small fix, and is posted once through a quoted heredoc. The review
step is `continue-on-error`, so a reviewer failure does not fail the workflow.

## Agentic preflight

[`.agentic-preflight.toml`](../.agentic-preflight.toml) compares the proposed
change with `main` (`base_ref = "main"`) and caps the inspected diff at 250,000
bytes. It excludes lock files; vendored or minified assets; generated snapshots
and protobuf files; `docs/runs/**`; `docs/results/data/**`;
`docs/results/portfolio-v2-evidence/**`; and the committed embedding cache.
Those exclusions keep named generated or high-volume data out of the review
diff; they do not change the test commands.

The `test` command runs the standard-library suite under Python 3.11 with `-S`,
then installs only `requirements-site.txt` into an isolated `uv` invocation for
the opt-in site-build tests. The `lint` command runs Ruff 0.14.2 and mypy 1.14.1
for both the main configuration and the evaluator configuration. As
[`CONTRIBUTING.md`](../CONTRIBUTING.md#checks) states, this file configures the
maintainer's local pre-push gate; outside contributors are not expected to run
agentic preflight itself, although the same checks run in CI.

## Recorded use of Claude, Codex, and evaluation models

The [dogfooding log](dogfooding.md) records manual runs from August 9 through
August 18, 2026; from August 20, the unattended GitHub Actions daily run and its
integrity reports are the operating record. The log includes Codex desktop runs such as the
[August 12 run](dogfooding.md#2026-08-12--codex-daily-dogfood-run), and a
[Claude Code CLI run](dogfooding.md#2026-08-13--claude-code-cli-dogfood-run)
using Claude Sonnet 5 at high effort. That Claude run exposed only `Read` and
`Write` and recorded no web search or fetch; later runner behavior uses the
provider-specific policy documented in the [design notes](design.md#the-code-owned-runner).
These entries also show how the tools were combined: Codex desktop was the
execution environment for several runs, while Claude Code CLI or Codex CLI could
serve as the runner's model provider. The current command examples name Claude
Sonnet 5 and GPT-5.6 Terra respectively in the [README](../README.md#generate-one).

The evaluator separates those operating records from benchmark claims. Its
[production-parity mode](../evaluator/README.md#production-parity-generation-path)
can use the Codex CLI, Claude Code CLI, or OpenRouter while reusing the production
projection, schemas, and renderer. The documented direct-Markdown injection run
used DeepSeek V4 Flash and Tencent HY3 through OpenRouter; its models, prompt
variants, counts, cost, and limits are recorded in the
[benchmark write-up](writeups/injection-benchmark-post.md#what-380-bought).
The evaluator's [label-review record](../evaluator/README.md#label-review) names
the model reviewers and distinguishes their output from independent human
review.

## Findings caught during automated review

- **PR #124:** Claude reported that `--strict` still checked only findings and
  `corpus["errors"]`, so a corpus degraded solely by undated items could return
  success despite the CLI contract. The PR changed the gate to use
  `corpus_schema.corpus_health_degraded()` and added an undated-only strict-mode
  regression test in [`tests/test_run_briefing.py`](../tests/test_run_briefing.py).

- **PR #156:** Claude found that the new `completed_with_errors` status widened
  single-manifest publication without applying the row validation already used
  for split manifests. The fix applies `_is_publishable_row()` to every row in
  the single-manifest path and tests that malformed provider-error rows are
  rejected in [`evaluator/publication.py`](../evaluator/publication.py) and
  [`evaluator/tests/test_evaluator.py`](../evaluator/tests/test_evaluator.py).

- **PR #158:** Claude found that the initial endpoint check did not stop
  `urllib` from forwarding an `Authorization` header to a redirect target. The
  shared provider transport now uses `_NoRedirect`, treats 3xx responses as
  non-transient errors, and has an end-to-end test proving the redirect target is
  not contacted in [`agent_runner/providers.py`](../agent_runner/providers.py)
  and [`tests/test_model_providers.py`](../tests/test_model_providers.py).
