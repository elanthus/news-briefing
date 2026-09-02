# Contributing

Thanks for helping improve news-briefing. Contributions should preserve the
project's central property: deterministic code constrains and checks the corpus,
while the model handles judgments that code cannot prove.

## Before you start

For a bug, open a focused issue with reproduction steps, expected behavior, and
actual behavior. For a substantial feature or a change to a trust boundary, open
an issue before writing code so the design and scope can be agreed first.

Report suspected vulnerabilities privately as described in
[SECURITY.md](SECURITY.md), not in a public issue.

## Development setup

Use Python 3.11 or newer. The briefing pipeline uses only the standard library, so
there is no package installation step:

```bash
git clone https://github.com/elanthus/news-briefing.git
cd news-briefing
python3 -S -m unittest -v
python3 -S -m unittest discover -s evaluator/tests -v
```

The optional evaluator development tools can be installed in an isolated
environment with:

```bash
uv sync --project evaluator --group dev
```

Static-site tests are intentionally excluded from default discovery. If you are
changing `build_site.py` or the generated site, install the pinned renderer and
run the opt-in module explicitly:

```bash
python3 -m pip install --requirement requirements-site.txt
python3 -m unittest -v tests.site_test_build
```

Smoke-test the evaluation harness (case loading, oracles, scoring, report
rendering) with zero provider calls:

```bash
python3 -S -m evaluator run --provider baseline=echo --trials 1 --output-dir "$(mktemp -d)/eval-smoke"
```

Never commit API keys, `.env` files, generated corpora, briefings, or evaluator
run artifacts.

## Repository map

| Path | What it is |
|---|---|
| [`fetch_news.py`](fetch_news.py) | Corpus fetcher: sources, windowing, relevance, deduplication, budgets, SSRF defense, XML defense |
| [`sources.json`](sources.json) | RSS feeds, Hacker News queries, and subreddit list read by the fetcher |
| [`corpus_schema.py`](corpus_schema.py) | Corpus contract (schema v6) and shared URL canonicalization |
| [`briefing-config.json`](briefing-config.json) | Section targets, eligible corpus categories, and exclusion-log sizes |
| [`briefing-runner-prompt.md`](briefing-runner-prompt.md) | Production structured-output prompt used by `run_briefing.py` and the daily fallback chain |
| [`briefing-prompt.md`](briefing-prompt.md) | Evaluator's legacy direct-Markdown prompt |
| [`agent_runner/`](agent_runner) | Provider adapters, citation projection, structured-output validation, deterministic repair, checkpoints |
| [`eval_briefing.py`](eval_briefing.py) | Standalone deterministic policy checker |
| [`run_daily_briefing.py`](run_daily_briefing.py) | Production fallback chain across three models until one run is `ready` |
| [`audit_manifest.py`](audit_manifest.py) | Text-free public corpus membership, provenance, canonical destinations, content hashes |
| [`corpus_storage.py`](corpus_storage.py) | Public private-storage marker for migration and archive-gap recovery |
| [`private_archive.py`](private_archive.py) | Authenticated encryption and bounded retention for operational corpora and diagnostics |
| [`restore_private_corpora.py`](restore_private_corpora.py) | Token-scoped restore of the newest encrypted GitHub Actions corpus archive |
| [`bootstrap_history.py`](bootstrap_history.py) | Seeds site history from selected, hash-verified committed run artifacts; used by the daily workflow |
| [`build_site.py`](build_site.py) | Static archive: briefings, integrity reports, public audit manifests |
| [`evaluator/`](evaluator) | Development-only benchmark: cases, oracles, judges, metrics, public evidence export |
| [`fixtures/`](fixtures) | Frozen corpus, briefing, configuration, and injection fixtures |

## Making a change

- Keep the core runtime compatible with Python 3.11+ and free of third-party
  dependencies.
- Add or update tests for behavior changes. Tests must not require network access.
- Treat fetched titles, summaries, sources, and URLs as untrusted input.
- Preserve explicit limits, typed failures, and corpus-health reporting; do not
  silently discard failures or weaken checks to make a run pass.
- Update the README, design notes, fixtures, or prompt documentation when a public
  contract or trust boundary changes.
- Keep changes focused. Avoid unrelated formatting or refactoring in the same pull
  request.

Changes to `briefing-runner-prompt.md`—the production prompt used by
`run_briefing.py` and the daily chain—need particular care, as do changes to
`briefing-prompt.md`, which serves the evaluator's direct-Markdown path. Changes
to security controls, fixture labels, or evaluator oracles also need particular
care. Explain the intended guarantee, add a regression case, and distinguish
deterministic enforcement from heuristic or model-evaluated behavior.

## Checks

Run the same offline checks used in continuous integration:

```bash
python3 -S -m unittest -v
python3 -S -m unittest discover -s evaluator/tests -v
python3 -m evaluator checker
uvx ruff@0.14.2 check .
uvx mypy@1.14.1
uvx mypy@1.14.1 --config-file evaluator/pyproject.toml evaluator
```

The evaluator checker validates checker behavior against the committed snapshot.
CI fails on snapshot drift; snapshot updates are opt-in via `--update-snapshot`.

CI runs the offline suites on Python 3.11 through 3.14, plus `ruff` and `mypy`
with `disallow_untyped_defs = true` from `pyproject.toml`, and a non-blocking
smoke test against live feeds.

CI and the agentic-preflight gate—the maintainer's local pre-push hook
configuration (`.agentic-preflight.toml`), which outside contributors are not
expected to run—also run `tests.site_test_build` with `requirements-site.txt`;
the module stays opt-in locally so the typical suite retains its no-install
contract.

The live-source smoke test is informational and is not required for a pull
request. If you run live model evaluations, disclose the provider, exact model,
prompt version, trial count, sampling controls, and any incomplete or failed runs.

## Pull requests

In the pull request description:

- explain the problem and the chosen solution;
- identify any changed guarantees, limitations, or compatibility concerns;
- list the checks you ran and their results; and
- link the relevant issue, if one exists.

Keep commits reviewable and use clear, imperative commit subjects. A pull request
should be small enough that its security and grounding implications can be
understood without unrelated context.

By contributing, you agree that your contribution is licensed under the project's
[MIT License](LICENSE).
