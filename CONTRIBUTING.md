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
python3 -m unittest -v
python3 -m unittest discover -s evaluator/tests -v
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

Never commit API keys, `.env` files, generated corpora, briefings, or evaluator
run artifacts.

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

Changes to `briefing-prompt.md`, security controls, fixture labels, or evaluator
oracles need particular care. Explain the intended guarantee, add a regression
case, and distinguish deterministic enforcement from heuristic or model-evaluated
behavior.

## Checks

Run the same offline checks used in continuous integration:

```bash
python3 -m unittest -v
python3 -m unittest discover -s evaluator/tests -v
uvx ruff@0.14.2 check .
uvx mypy@1.14.1
uvx mypy@1.14.1 --config-file evaluator/pyproject.toml evaluator
```

CI and the agentic-preflight gate also run `tests.site_test_build` with
`requirements-site.txt`; the module stays opt-in locally so the typical suite
retains its no-install contract.

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
